"""Merge a deployment's live-signal log into the repository's copy.

    PYTHONPATH=python python -m live.sync_live_signals /tmp/from-vps.jsonl
    PYTHONPATH=python python -m live.sync_live_signals /tmp/from-vps.jsonl --dry-run

## The problem this exists to solve

`runs/live_signals.jsonl` is the operational audit trail for
`daily-tsmom-ensemble`, and it is **deliberately git-tracked** -- see
`.gitignore`'s `!runs/live_signals.jsonl` and
`.planning/paper-trading-b-signal-runner.md`. The repository is the
record.

That was coherent while the signal runner ran on the same machine as the
repository. It stopped being coherent the moment the paper-trading loops
moved to a VPS (2026-09-05, `.planning/ops-vps-deployment.md`), because
then **two independent things wrote one file with nothing between them**:
`git checkout`/`git pull` on one side, and the deployment's own cron on
the other.

Both failure modes were real, not hypothetical, and were observed
together on 2026-09-06:

1. **Every deploy conflicted.** The server sat three commits behind with
   `M runs/live_signals.jsonl` in its working tree, so `git pull`
   refused. The tempting fix -- `git checkout runs/live_signals.jsonl`
   -- silently destroys operational history.
2. **The only copy of two days of audit trail lived on a free-tier
   e2-micro.** Deleting that VM would have deleted the record of what
   the system decided on 2026-09-05 and 2026-09-06. An audit trail whose
   sole copy is on the machine being audited is not an audit trail.

So the deployment now writes to `var/live/live_signals.jsonl` (under the
already-gitignored `var/live/`, set by
`scripts/paper-trading-daily-signal.sh`), never to a tracked file, and
this tool is the one path from there into the repository -- run by a
human, reviewed as a normal PR.

## What it guarantees, and why each one is here

- **Append-only.** Every record already in the tracked file is still
  there, in the same order, afterwards. Asserted rather than assumed:
  "it only appends, by construction" is exactly the shape of claim that
  has been wrong in this project before.
- **Idempotent.** Records are identified by `run_id`, so syncing the
  same deployment file twice adds nothing the second time. An operator
  who is unsure whether a sync already ran can just run it again.
- **Conflicts refuse rather than resolve.** One `run_id` carrying two
  different payloads means the same run was logged twice with different
  results. There is no correct automatic answer, so it aborts and names
  the id.
- **`--dry-run` writes nothing at all** -- no output file, no temp file,
  no marker. `research.change_check.check_readonly_path_is_pure` exists
  because `generate_mock_signal --dry-run` advanced persisted state and
  so changed the *next real* signal.
- **Fail closed on malformed input.** A line that is not JSON, or a
  record with no `run_id` or no `logged_at`, aborts the whole sync. A
  partial merge of an audit trail is worse than a refused one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The repository's copy: the record. Relative on purpose -- it is a path
# *within the repository*, and every caller runs from the repository
# root. `main` resolves it before use so an unexpected working directory
# surfaces as "no such file" rather than as a second, stray audit trail
# somewhere else on disk.
TRACKED_PATH = "runs/live_signals.jsonl"

# Where a deployment writes instead. Gitignored (`var/live/`), so the
# running system can never dirty the working tree of its own checkout.
DEPLOYMENT_PATH = "var/live/live_signals.jsonl"


class SyncRefused(RuntimeError):
    """The merge would not have been safe, so nothing was written."""


@dataclass(frozen=True)
class SyncPlan:
    """What a sync would do. Produced without writing anything."""

    kept: int
    """Records already in the tracked file. Never changes."""

    added: list[dict[str, Any]]
    """New records, in `logged_at` order, to be appended."""

    already_present: int
    """Deployment records the tracked file already had. The idempotence
    count -- a second run of the same sync reports all of them here and
    nothing in `added`."""

    def describe(self) -> str:
        if not self.added:
            return (
                f"nothing to do: all {self.already_present} deployment "
                f"record(s) are already in the tracked log ({self.kept} total)"
            )
        first = self.added[0].get("logged_at")
        last = self.added[-1].get("logged_at")
        return (
            f"{len(self.added)} new record(s) to append "
            f"({first} .. {last}); {self.already_present} already present; "
            f"{self.kept} kept unchanged"
        )


def read_records(path: str | Path) -> list[dict[str, Any]]:
    """Parse a JSONL log, refusing anything it cannot fully understand.

    A missing file is an empty log -- the tracked copy legitimately does
    not exist before the first sync. A malformed *line* is not: it means
    the file was truncated mid-write or is not what we think it is, and
    merging half an audit trail is worse than refusing.
    """
    path = Path(path)
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SyncRefused(f"{path}:{lineno} is not valid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise SyncRefused(f"{path}:{lineno} is not a JSON object")
        for required in ("run_id", "logged_at"):
            if not record.get(required):
                raise SyncRefused(
                    f"{path}:{lineno} has no {required!r}. Records are identified "
                    f"by run_id and ordered by logged_at; a record missing either "
                    f"cannot be merged safely."
                )
        records.append(record)
    return records


def plan_sync(
    tracked: list[dict[str, Any]], deployment: list[dict[str, Any]]
) -> SyncPlan:
    """Decide what to append, without touching anything.

    Raises `SyncRefused` on a duplicated `run_id` within one file, or on
    one `run_id` carrying different content in the two files.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for source, records in (("tracked log", tracked), ("deployment log", deployment)):
        seen: set[str] = set()
        for record in records:
            run_id = record["run_id"]
            if run_id in seen:
                raise SyncRefused(
                    f"the {source} contains run_id {run_id!r} twice. A run is "
                    f"logged once; two entries means something re-ran and "
                    f"overwrote its own history."
                )
            seen.add(run_id)

    for record in tracked:
        by_id[record["run_id"]] = record

    added: list[dict[str, Any]] = []
    already_present = 0
    for record in deployment:
        run_id = record["run_id"]
        existing = by_id.get(run_id)
        if existing is None:
            added.append(record)
            continue
        if existing != record:
            raise SyncRefused(
                f"run_id {run_id!r} is in both logs with different content. The "
                f"same run cannot have produced two different results; resolve "
                f"this by hand and say which is real. Nothing was written."
            )
        already_present += 1

    # The APPENDED BLOCK is ordered by `logged_at`. The merged file as a
    # whole is not guaranteed chronological, and that is deliberate:
    # append-only wins over ordering, so a deployment record older than
    # the tracked log's last entry still lands at the end rather than
    # being inserted. Stated explicitly because a reader who assumes the
    # whole file is chronological would draw wrong conclusions from it --
    # sort by `logged_at` before reasoning about sequence.
    added.sort(key=lambda record: record["logged_at"])

    return SyncPlan(kept=len(tracked), added=added, already_present=already_present)


def apply_sync(
    plan: SyncPlan, tracked: list[dict[str, Any]], tracked_path: str | Path
) -> None:
    """Write the merged log atomically, refusing if it changed underneath.

    The check here is deliberately **not** "is `tracked + added` a
    superset of `tracked`". That is a tautology -- it is true by the
    shape of the expression and can never fire, which is precisely the
    inert-guard failure this project has now hit four times. A guard
    that cannot fail is worse than none, because it converts "we might
    be exposed" into "we are certain we are not".

    The real hazard is time-of-check-to-time-of-use: the sync reads the
    tracked file, plans against it, and writes seconds later. If
    anything appended in between -- an operator running two syncs, or a
    cron job on a machine where the deployment path was not yet
    repointed -- the plan is stale and writing it would **erase** those
    records. So the file is re-read immediately before the rename and
    must still be exactly what was planned against.

    On any refusal the tracked file is untouched: the merged content is
    written to a temp file and renamed only at the very end.
    """
    path = Path(tracked_path)

    # Full records, not just the `run_id` list. Comparing ids alone
    # passes when a record's *content* changed while its id stayed put,
    # and the very next line writes `tracked` back over it -- silently
    # reverting an audit record, which is the one thing this module
    # promises never to do. The docstring above already claimed "exactly
    # what was planned against"; an id-only comparison was weaker than
    # its own stated contract.
    on_disk = read_records(path)
    if on_disk != tracked:
        raise SyncRefused(
            f"{path} changed after the sync was planned: it now holds "
            f"{len(on_disk)} record(s) where the plan saw {len(tracked)}"
            + (
                " (same run_ids, different content)"
                if [r["run_id"] for r in on_disk] == [r["run_id"] for r in tracked]
                else ""
            )
            + ". Writing the plan would have erased or reverted whatever "
            "arrived in between. Re-run the sync. Nothing was written."
        )

    merged = tracked + plan.added

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".sync.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            for record in merged:
                # `sort_keys` matches what `research.experiment_log`
                # already writes, so a synced record is byte-identical to
                # one written locally and the diff shows only new lines.
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        # The rename itself is metadata, and metadata is not durable
        # until the containing directory is synced. Without this a power
        # loss immediately after a sync can lose the rename and leave
        # the previous file -- cheap insurance on an audit trail.
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Merge a deployment's live-signal log into the repository's "
            "git-tracked copy. Append-only and idempotent."
        )
    )
    parser.add_argument(
        "deployment_log",
        help=(
            f"the file pulled off the deployment, normally {DEPLOYMENT_PATH} "
            f"on the VPS"
        ),
    )
    parser.add_argument("--tracked", default=TRACKED_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be appended and write nothing at all",
    )
    args = parser.parse_args(argv)

    try:
        tracked = read_records(args.tracked)
        deployment = read_records(args.deployment_log)
        plan = plan_sync(tracked, deployment)
    except SyncRefused as exc:
        print(f"sync refused: {exc}", file=sys.stderr)
        return 1

    print(plan.describe())

    if args.dry_run:
        print("--dry-run: nothing written")
        return 0

    if not plan.added:
        # Deliberately does not rewrite the file to itself. A no-op that
        # still touches the tracked audit trail would make `git status`
        # noisy for no reason and invite exactly the "just check it out"
        # reflex this tool exists to remove.
        return 0

    try:
        apply_sync(plan, tracked, args.tracked)
    except SyncRefused as exc:
        print(f"sync refused: {exc}", file=sys.stderr)
        return 1

    print(f"appended {len(plan.added)} record(s) to {args.tracked}")
    print("review the diff and commit it through the normal PR flow")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
