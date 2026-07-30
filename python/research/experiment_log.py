"""Append-only experiment log: `runs/experiments.jsonl`.

See CLAUDE.md's "Strategy Research Operational Design" section,
"Experiment-tracking format" and "Durability" subsections, for the full
design this module implements.

One `record_type: "backtest_run"` entry per `run_walk_forward` call
(`log_run`, called automatically as that function's last action -- see
`research/walkforward.py` -- not something a caller can forget to do), and
one `record_type: "holdout_access"` entry per successful
`load_holdout_klines` call (`log_holdout_access`, called from
`research/holdout.py`). Both record types interleave in the one file, in
write order.

**Durability**: each write is exactly one `write()` call of a single
complete JSON object plus `\\n`, followed by an explicit `flush()` +
`os.fsync()` before the call returns -- a call that returns successfully
is actually durable on disk, not just buffered in a Python/OS write
buffer, before its caller (including `research/holdout.py`'s single-
access claim) proceeds.

**What this does NOT provide**: safe interleaving of writes from multiple
concurrent processes. The real mitigation is a single-writer assumption
(one research process appending to this file at a time), not file
locking -- nothing in this project's actual usage pattern needs concurrent
writers yet. See CLAUDE.md for when to revisit that (add real locking,
e.g. `flock` on a sentinel file, if the assumption is ever violated).

`read_records` tolerates a truncated *final* line (the on-disk signature
of a write interrupted mid-line by a crash/kill) by skipping it rather
than raising -- the record it would have described never completed and
has no other side effects to reconcile. A malformed line that is NOT the
final one is real corruption, not the documented interrupted-write
scenario, and is allowed to raise.
"""

import json
import os
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

# Relative to the process's current working directory -- same convention
# as `data.backfill.DEFAULT_DB_PATH` (this project's existing precedent
# for a runtime-data default path: a plain relative string, not a
# `Path(__file__)`-derived absolute one). Callers running for real are
# expected to run from the repo root, same as `backfill.py`'s CLI; tests
# always override via `runs_path=tmp_path / ...` and never rely on this
# default. `.gitignore` already has a `runs/` entry (added before this
# design existed).
DEFAULT_RUNS_PATH = "runs/experiments.jsonl"


def _json_default(obj: Any) -> Any:
    """`json.dumps(..., default=...)` hook: makes `Decimal` and
    `datetime` values round-trippable-as-text (same reasoning as
    `schemas/_types.py`'s `PositiveDecimalString` -- a JSON number would
    round-trip Decimal lossily through float) and dataclasses (e.g. a
    caller passing a `Metrics`/`ClosedTrade` instance somewhere in
    `params`/`fold_results` without pre-serializing it) plain-dict-able,
    without every call site needing to remember to stringify/asdict
    itself.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _git_head_sha() -> str | None:
    """Best-effort `git rev-parse HEAD`. `None` (never a raised
    exception) if git isn't available or this isn't a git checkout --
    logging a run must never fail just because `code_version` couldn't be
    determined.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def require_complete_preregistration_pair(
    preregistration_id: str | None,
    preregistration_sha256: str | None,
) -> None:
    """Raise `ValueError` unless the two pre-registration provenance fields
    are both supplied or both absent.

    Public (no leading underscore) on purpose: `research/walkforward.py`
    calls it at *its* entry point, before running any folds, so a caller
    mistake fails before a completed run's results would be thrown away at
    logging time -- the same reasoning as that function's own `bars_per_day`
    check. `log_run` calls it too, so the invariant holds for every writer,
    not only the ones that remembered.
    """
    if (preregistration_id is None) != (preregistration_sha256 is None):
        raise ValueError(
            "preregistration_id and preregistration_sha256 must be supplied together or not "
            f"at all (got id={preregistration_id!r}, sha256={preregistration_sha256!r}) -- a "
            "logged pre-registration claim without its integrity hash cannot be checked against "
            "the committed file, and a hash with no id cannot be attributed to one"
        )


def _append_record(record: dict, runs_path: str | Path) -> dict:
    path = Path(runs_path)
    is_new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=_json_default, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())

    if is_new_file:
        # `os.fsync(f.fileno())` above only guarantees the file's own
        # data made it to disk -- if this call is what created the file,
        # the directory entry pointing to it is a separate piece of
        # metadata that a crash could still lose (the well-known POSIX
        # "fsync doesn't sync the containing directory" gap). This log is
        # the sole basis for `research.holdout`'s single-access holdout
        # claim, so a lost directory entry after the very first write
        # could in theory let a second `holdout_access` write for the
        # same strategy_id go unnoticed. `os.open`/`os.fsync` on a
        # directory fd is POSIX-only (this project's deployment target
        # per CLAUDE.md); best-effort on any platform/filesystem that
        # doesn't support it, since the file-level durability above
        # already holds regardless.
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    return record


def log_run(
    *,
    run_id: str,
    strategy_id: str,
    strategy_version: str,
    params: Mapping[str, Any],
    fold_results: Sequence[Mapping[str, Any]],
    aggregate_metrics: Mapping[str, Any],
    data_range: Mapping[str, Any],
    walk_forward_config: Mapping[str, Any],
    fee_bps: Any,
    slippage_bps: Any,
    is_holdout_run: bool,
    parent_run_id: str | None = None,
    candidate_index: int | None = None,
    total_candidates: int | None = None,
    strategy_family: str | None = None,
    preregistration_id: str | None = None,
    preregistration_sha256: str | None = None,
    runs_path: str | Path = DEFAULT_RUNS_PATH,
) -> dict:
    """Append one `record_type: "backtest_run"` entry. Called automatically
    as `run_walk_forward`'s last action -- see `research/walkforward.py`.

    `parent_run_id`/`candidate_index`/`total_candidates` are the grid-
    search lineage fields from CLAUDE.md's design: all `None` for a
    standalone run (everything this task produces), set by Task D's grid
    search for each candidate backtest it spawns.

    `strategy_family` (Strategy Research Task P, `.planning/sr-p-trial-
    accounting.md`) records which research family this run belongs to, so
    renaming a `strategy_id` between runs can't reset its data-snooping
    history. **When `None`, the key is omitted from the record entirely** --
    deliberately not written as `null` the way `parent_run_id` is. That
    difference is functional, not cosmetic: "key absent" then unambiguously
    means "pre-lineage record, attribute via `research.lineage`'s curated
    map", while "key present" means "trust the record". A `null` would
    collide the two cases. Every pre-Task-P record in
    `runs/experiments.jsonl` is unaffected and still reads back identically.

    `preregistration_id`/`preregistration_sha256` (Strategy Research Task S,
    `.planning/sr-s-preregistration.md`) record which committed
    pre-registration a run was made under, and the SHA-256 of that file's
    bytes **as read at run time** -- so a later edit to the registration
    becomes detectable by comparing the file against what the log says was
    hashed. Both follow `strategy_family`'s "key omitted entirely when
    `None`" convention, for the same reason: *absent* must unambiguously
    mean "this run was not made under a pre-registration", which a `null`
    would collide with "made under one, but nobody recorded which".

    The two must be supplied **together or not at all** -- a record claiming
    a pre-registration with no integrity hash (or a hash with nothing to
    attribute it to) is worse than one claiming nothing, because it looks
    like provenance while proving none. Same "they describe the same thing,
    so half of them is a caller mistake" rule
    `research/eligibility.py::_resolve_moments` applies to
    skewness/kurtosis. Enforced here, at the writer, so no caller can
    bypass it; `run_walk_forward` re-checks at its own entry point so a
    completed run's fold results are not thrown away at logging time.

    Returns the record actually written (with all types still Python-
    native, e.g. `Decimal`/`datetime` un-stringified) for a caller that
    wants to inspect what was logged without re-parsing the file.
    """
    require_complete_preregistration_pair(preregistration_id, preregistration_sha256)
    record = {
        "record_type": "backtest_run",
        "run_id": run_id,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "params": dict(params),
        "fold_results": list(fold_results),
        "aggregate_metrics": dict(aggregate_metrics),
        "data_range": dict(data_range),
        "walk_forward_config": dict(walk_forward_config),
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "is_holdout_run": is_holdout_run,
        "code_version": _git_head_sha(),
        "parent_run_id": parent_run_id,
        "candidate_index": candidate_index,
        "total_candidates": total_candidates,
    }
    # Only present when a caller actually supplied one -- see this
    # function's docstring for why "key absent" and "key null" must stay
    # distinguishable here.
    if strategy_family is not None:
        record["strategy_family"] = strategy_family
    if preregistration_id is not None:
        # Validated as a complete pair above, so the sha is non-None here.
        record["preregistration_id"] = preregistration_id
        record["preregistration_sha256"] = preregistration_sha256
    return _append_record(record, runs_path)


def log_holdout_access(
    *,
    strategy_id: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    force_reclaim_reason: str | None = None,
    runs_path: str | Path = DEFAULT_RUNS_PATH,
) -> dict:
    """Append one `record_type: "holdout_access"` entry. Called from
    `research/holdout.py::load_holdout_klines` after a successful load --
    this record IS the single-access claim `load_holdout_klines` enforces
    (scanned via `read_records`, not a separate claim table).
    """
    record = {
        "record_type": "holdout_access",
        "accessed_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": strategy_id,
        "symbol": symbol,
        "interval": interval,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "force_reclaim_reason": force_reclaim_reason,
    }
    return _append_record(record, runs_path)


def read_records(runs_path: str | Path = DEFAULT_RUNS_PATH) -> Iterator[dict]:
    """Yield every record in `runs_path`, in file (write) order.

    A missing file yields nothing (an empty log is a valid, unremarkable
    state -- e.g. before the first run ever happens) rather than raising.
    A final line that fails to parse as JSON is silently skipped (see
    module docstring's "Durability" note) rather than raising; any
    earlier malformed line is treated as real corruption and raises
    `json.JSONDecodeError`.
    """
    path = Path(runs_path)
    if not path.exists():
        return

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    last_index = len(lines) - 1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            yield json.loads(stripped)
        except json.JSONDecodeError:
            if index == last_index:
                continue
            raise
