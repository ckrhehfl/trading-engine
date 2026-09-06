"""The sync must never lose an audit record, and must never invent one.

Written against the real incident it exists for: two days of live-signal
history existed only on a free-tier VM whose repository checkout could
not be updated because the running system had dirtied a tracked file.

The load-bearing tests are the refusals. A merge tool that silently does
something reasonable-looking with a conflict is worse than one that
stops, because an audit trail's whole value is that nobody quietly
adjusted it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from live.sync_live_signals import (
    SyncRefused,
    apply_sync,
    main,
    plan_sync,
    read_records,
)


def record(run_id: str, logged_at: str, **extra):
    return {"run_id": run_id, "logged_at": logged_at, "record_type": "backtest_run", **extra}


def write_log(path: Path, records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
    )
    return path


class TestReadRecords:
    def test_a_missing_tracked_log_is_an_empty_log(self, tmp_path):
        """Legitimate before the first sync ever runs."""
        assert read_records(tmp_path / "absent.jsonl") == []

    def test_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "log.jsonl"
        path.write_text(
            json.dumps(record("a", "2026-09-05T00:00:00Z")) + "\n\n\n", encoding="utf-8"
        )
        assert len(read_records(path)) == 1

    def test_a_truncated_line_refuses_rather_than_merging_half_a_log(self, tmp_path):
        path = tmp_path / "log.jsonl"
        path.write_text(
            json.dumps(record("a", "2026-09-05T00:00:00Z")) + "\n{\"run_id\": \"b\"",
            encoding="utf-8",
        )
        with pytest.raises(SyncRefused, match="not valid JSON"):
            read_records(path)

    @pytest.mark.parametrize("missing", ["run_id", "logged_at"])
    def test_a_record_without_an_identity_or_a_time_is_refused(self, tmp_path, missing):
        bad = record("a", "2026-09-05T00:00:00Z")
        del bad[missing]
        path = write_log(tmp_path / "log.jsonl", [bad])
        with pytest.raises(SyncRefused, match=missing):
            read_records(path)

    def test_a_bare_json_array_is_not_a_jsonl_log(self, tmp_path):
        path = tmp_path / "log.jsonl"
        path.write_text("[1, 2, 3]\n", encoding="utf-8")
        with pytest.raises(SyncRefused, match="not a JSON object"):
            read_records(path)


class TestPlanSync:
    def test_the_real_case_two_records_stranded_on_the_vps(self):
        """The 2026-09-06 incident, in miniature: the tracked log is an
        exact prefix of the deployment's, which is the normal shape."""
        tracked = [record(str(i), f"2026-09-0{i}T00:00:00Z") for i in range(1, 4)]
        deployment = tracked + [
            record("4", "2026-09-05T16:00:03Z"),
            record("5", "2026-09-06T00:00:04Z"),
        ]
        plan = plan_sync(tracked, deployment)
        assert plan.kept == 3
        assert plan.already_present == 3
        assert [r["run_id"] for r in plan.added] == ["4", "5"]

    def test_running_it_twice_adds_nothing(self):
        """Idempotence. An operator unsure whether a sync already ran must
        be able to just run it again."""
        shared = [record("1", "2026-09-01T00:00:00Z")]
        plan = plan_sync(shared, shared)
        assert plan.added == [] and plan.already_present == 1
        assert "nothing to do" in plan.describe()

    def test_new_records_come_out_in_chronological_order(self):
        deployment = [
            record("late", "2026-09-06T00:00:00Z"),
            record("early", "2026-09-05T00:00:00Z"),
        ]
        plan = plan_sync([], deployment)
        assert [r["run_id"] for r in plan.added] == ["early", "late"]

    def test_one_run_id_with_two_different_payloads_is_refused(self):
        """There is no correct automatic answer. Picking one silently
        would rewrite history to whichever file was passed second."""
        tracked = [record("1", "2026-09-01T00:00:00Z", sharpe_ratio=0.5)]
        deployment = [record("1", "2026-09-01T00:00:00Z", sharpe_ratio=-0.9)]
        with pytest.raises(SyncRefused, match="different content"):
            plan_sync(tracked, deployment)

    def test_a_duplicated_run_id_within_one_file_is_refused(self):
        dupes = [record("1", "2026-09-01T00:00:00Z"), record("1", "2026-09-02T00:00:00Z")]
        with pytest.raises(SyncRefused, match="twice"):
            plan_sync([], dupes)

    def test_a_deployment_log_that_is_behind_loses_nothing(self):
        """The server was rebuilt and its log is shorter. The tracked
        record must survive that untouched -- this is the case where a
        naive `cp` would destroy the audit trail."""
        tracked = [record(str(i), f"2026-09-0{i}T00:00:00Z") for i in range(1, 6)]
        plan = plan_sync(tracked, tracked[:2])
        assert plan.added == [] and plan.kept == 5


class TestApplySync:
    def test_existing_records_survive_byte_for_byte_in_order(self, tmp_path):
        path = tmp_path / "runs" / "live_signals.jsonl"
        tracked = [record(str(i), f"2026-09-0{i}T00:00:00Z") for i in range(1, 4)]
        write_log(path, tracked)
        before = path.read_text(encoding="utf-8")

        plan = plan_sync(tracked, tracked + [record("4", "2026-09-04T00:00:00Z")])
        apply_sync(plan, tracked, path)

        after = path.read_text(encoding="utf-8")
        assert after.startswith(before), "the merge was not append-only"
        assert len(after.splitlines()) == 4

    def test_it_refuses_when_the_file_grew_after_the_plan_was_made(self, tmp_path):
        """The real hazard, and the only one the guard can actually
        catch: something appended between the read and the write.
        Applying the stale plan would erase it.

        Checking "is tracked+added a superset of tracked" instead would
        be a tautology that never fires -- the first version of this
        guard was exactly that.
        """
        path = tmp_path / "log.jsonl"
        tracked = [record("1", "2026-09-01T00:00:00Z")]
        write_log(path, tracked)

        plan = plan_sync(tracked, tracked + [record("2", "2026-09-02T00:00:00Z")])

        # A cron tick lands in between.
        write_log(path, tracked + [record("cron", "2026-09-01T12:00:00Z")])
        before = path.read_bytes()

        with pytest.raises(SyncRefused, match="changed after the sync was planned"):
            apply_sync(plan, tracked, path)
        assert path.read_bytes() == before, "a refused sync must write nothing"

    def test_it_refuses_when_the_file_was_replaced_wholesale(self, tmp_path):
        path = tmp_path / "log.jsonl"
        tracked = [record("1", "2026-09-01T00:00:00Z")]
        write_log(path, tracked)
        plan = plan_sync(tracked, tracked + [record("2", "2026-09-02T00:00:00Z")])

        write_log(path, [record("different", "2026-09-01T00:00:00Z")])
        with pytest.raises(SyncRefused, match="changed after the sync was planned"):
            apply_sync(plan, tracked, path)

    def test_it_refuses_when_content_changed_but_the_ids_did_not(self, tmp_path):
        """The id-only comparison the first version used passes here, and
        the next line writes the stale content back -- silently reverting
        an audit record. Same run_ids, different payload."""
        path = tmp_path / "log.jsonl"
        tracked = [record("1", "2026-09-01T00:00:00Z", sharpe_ratio=0.5)]
        write_log(path, tracked)
        plan = plan_sync(tracked, tracked + [record("2", "2026-09-02T00:00:00Z")])

        corrected = [record("1", "2026-09-01T00:00:00Z", sharpe_ratio=-0.9)]
        write_log(path, corrected)
        before = path.read_bytes()

        with pytest.raises(SyncRefused, match="same run_ids, different content"):
            apply_sync(plan, tracked, path)
        assert path.read_bytes() == before

    def test_no_temp_file_is_left_behind(self, tmp_path):
        """The `.lock` sidecar is expected and stays; a `.tmp` must not.

        Asserted as "no temp file" rather than "exactly one file",
        because the second form would break the next time anything
        legitimately lands beside the log -- and then get relaxed
        without anyone re-checking what it was actually for."""
        path = tmp_path / "log.jsonl"
        tracked = [record("1", "2026-09-01T00:00:00Z")]
        write_log(path, tracked)
        plan = plan_sync(tracked, tracked + [record("2", "2026-09-02T00:00:00Z")])
        apply_sync(plan, tracked, path)
        assert [p.name for p in tmp_path.glob("*.tmp")] == []
        assert path.exists()


class TestCli:
    def test_dry_run_writes_absolutely_nothing(self, tmp_path, capsys):
        """`check_readonly_path_is_pure`'s scar: `--dry-run` on the mock
        signal generator advanced persisted state and so changed the next
        real signal."""
        tracked = tmp_path / "runs" / "live_signals.jsonl"
        write_log(tracked, [record("1", "2026-09-01T00:00:00Z")])
        deployment = write_log(
            tmp_path / "vps.jsonl",
            [record("1", "2026-09-01T00:00:00Z"), record("2", "2026-09-02T00:00:00Z")],
        )
        before = tracked.read_bytes()
        listing_before = sorted(p.name for p in tracked.parent.iterdir())

        assert main([str(deployment), "--tracked", str(tracked), "--dry-run"]) == 0

        assert tracked.read_bytes() == before
        assert sorted(p.name for p in tracked.parent.iterdir()) == listing_before
        assert "nothing written" in capsys.readouterr().out

    def test_dry_run_predicts_exactly_what_the_real_run_then_does(self, tmp_path, capsys):
        """The other half of a read-only path being useful: it has to be
        right, not merely harmless."""
        tracked = tmp_path / "log.jsonl"
        write_log(tracked, [record("1", "2026-09-01T00:00:00Z")])
        deployment = write_log(
            tmp_path / "vps.jsonl",
            [record("1", "2026-09-01T00:00:00Z"), record("2", "2026-09-02T00:00:00Z")],
        )

        main([str(deployment), "--tracked", str(tracked), "--dry-run"])
        predicted = capsys.readouterr().out.splitlines()[0]

        main([str(deployment), "--tracked", str(tracked)])
        actual = capsys.readouterr().out.splitlines()[0]

        assert predicted == actual
        assert len(tracked.read_text(encoding="utf-8").splitlines()) == 2

    def test_a_second_run_changes_nothing_on_disk(self, tmp_path):
        tracked = tmp_path / "log.jsonl"
        write_log(tracked, [record("1", "2026-09-01T00:00:00Z")])
        deployment = write_log(
            tmp_path / "vps.jsonl",
            [record("1", "2026-09-01T00:00:00Z"), record("2", "2026-09-02T00:00:00Z")],
        )

        main([str(deployment), "--tracked", str(tracked)])
        after_first = tracked.read_bytes()
        main([str(deployment), "--tracked", str(tracked)])
        assert tracked.read_bytes() == after_first

    def test_a_conflict_exits_nonzero_and_writes_nothing(self, tmp_path, capsys):
        tracked = tmp_path / "log.jsonl"
        write_log(tracked, [record("1", "2026-09-01T00:00:00Z", sharpe_ratio=0.5)])
        before = tracked.read_bytes()
        deployment = write_log(
            tmp_path / "vps.jsonl",
            [record("1", "2026-09-01T00:00:00Z", sharpe_ratio=-0.9)],
        )

        assert main([str(deployment), "--tracked", str(tracked)]) == 1
        assert tracked.read_bytes() == before
        assert "sync refused" in capsys.readouterr().err

    def test_nothing_to_do_leaves_the_file_untouched_not_rewritten(self, tmp_path):
        """A no-op that still rewrites the tracked file would make
        `git status` dirty for no reason -- and a dirty tracked audit
        trail is the exact condition that started this."""
        tracked = tmp_path / "log.jsonl"
        write_log(tracked, [record("1", "2026-09-01T00:00:00Z")])
        mtime_before = tracked.stat().st_mtime_ns
        deployment = write_log(tmp_path / "vps.jsonl", [record("1", "2026-09-01T00:00:00Z")])

        assert main([str(deployment), "--tracked", str(tracked)]) == 0
        assert tracked.stat().st_mtime_ns == mtime_before


def test_the_tracked_path_default_is_the_committed_audit_trail():
    """If this constant drifts, a sync silently builds a second audit
    trail somewhere else and the real one stops being updated."""
    from live.sync_live_signals import DEPLOYMENT_PATH, TRACKED_PATH

    assert TRACKED_PATH == "runs/live_signals.jsonl"
    assert DEPLOYMENT_PATH.startswith("var/live/"), (
        "the deployment path must sit under the gitignored var/live/, or the "
        "running system dirties its own checkout again"
    )


class TestConcurrentSyncs:
    """Two operators, or two terminals, running a sync at the same time.

    The TOCTOU re-read alone narrows that window without closing it:
    both processes can read the same `tracked`, both pass the check, and
    the second `os.replace` silently drops the first one's records.

    The property asserted is **no record is ever lost** -- not "everyone
    succeeds". Under the lock the loser re-reads inside the critical
    section, sees the file changed, and refuses. Refusing is correct;
    clobbering is not. So the file must end up holding exactly the
    original records plus one per process that reported success.

    Real subprocesses, because `flock` is a kernel lock between
    processes -- threads in one interpreter would share the descriptor
    and prove nothing.
    """

    @staticmethod
    def _run_one(python, cwd, env, deployment, tracked_path):
        return subprocess.run(
            [python, "-m", "live.sync_live_signals", str(deployment),
             "--tracked", str(tracked_path)],
            cwd=cwd, env=env, capture_output=True, text=True,
        )

    def test_concurrent_syncs_never_lose_a_record(self, tmp_path):
        import concurrent.futures

        tracked_path = tmp_path / "log.jsonl"
        base = [record("base", "2026-09-01T00:00:00Z")]
        write_log(tracked_path, base)

        workers = 8
        deployments = []
        for i in range(workers):
            deployment = tmp_path / f"vps-{i}.jsonl"
            write_log(deployment, base + [record(f"new-{i}", f"2026-09-02T00:00:0{i}Z")])
            deployments.append(deployment)

        repo_python = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(repo_python)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(
                lambda d: self._run_one(sys.executable, repo_python, env, d, tracked_path),
                deployments,
            ))

        succeeded = [r for r in results if r.returncode == 0]
        refused = [r for r in results if r.returncode == 1]
        assert len(succeeded) + len(refused) == workers, (
            "a sync exited with something other than 0 or 1: "
            + str([r.returncode for r in results])
        )
        for r in refused:
            assert "sync refused" in r.stderr, r.stderr

        final = read_records(tracked_path)
        assert final[0] == base[0], "the original record must survive untouched"

        # The core assertion. A lost update shows up here as fewer
        # records than successes -- which is exactly what happens with
        # the lock removed.
        appended = [r for r in final if r["run_id"] != "base"]
        added_by_successes = sum(
            0 if "nothing to do" in r.stdout else 1 for r in succeeded
        )
        assert len(appended) == added_by_successes, (
            f"{added_by_successes} run(s) reported appending a record but the "
            f"file holds {len(appended)} -- a concurrent write was lost"
        )
        assert len({r["run_id"] for r in final}) == len(final), "duplicate run_id"

    def test_no_temp_files_survive_concurrent_syncs(self, tmp_path):
        """A fixed `.sync.tmp` name is a second collision, independent of
        the lock. Per-process names keep it that way if the lock is ever
        changed."""
        import concurrent.futures

        tracked_path = tmp_path / "log.jsonl"
        base = [record("base", "2026-09-01T00:00:00Z")]
        write_log(tracked_path, base)
        deployments = []
        for i in range(4):
            d = tmp_path / f"vps-{i}.jsonl"
            write_log(d, base + [record(f"new-{i}", f"2026-09-02T00:00:0{i}Z")])
            deployments.append(d)

        repo_python = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(repo_python)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(
                lambda d: self._run_one(sys.executable, repo_python, env, d, tracked_path),
                deployments,
            ))

        assert [p.name for p in tmp_path.glob("*.tmp")] == []
