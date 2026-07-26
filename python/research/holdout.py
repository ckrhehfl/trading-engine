"""Holdout-split mechanics: a git-tracked cutoff config
(`configs/research/holdout.json`) plus two differently-named loader
functions.

See CLAUDE.md's "Strategy Research Operational Design" section,
"Holdout-split mechanics" subsection, for the full design this module
implements.

- `load_research_klines(start_ms, end_ms)` -- the default path used
  everywhere. Unconditionally clamps `end_ms` to
  `min(end_ms, holdout_cutoff_ms)`, logging a warning when it actually
  clamps something.
- `load_holdout_klines(start_ms, end_ms, *,
  i_understand_this_is_holdout_data=True, strategy_id=...)` -- raises
  unless the keyword is explicitly `True`. Returns only data at/after the
  cutoff. Enforces a single access per `strategy_id`, derived directly
  from `runs/experiments.jsonl` (via `research.experiment_log`) rather
  than a separate claim table: before returning data, it scans the log
  for an existing `holdout_access` record for this `strategy_id` and
  raises `HoldoutAlreadyClaimedError` if one exists; otherwise it
  proceeds and appends its own `holdout_access` record before returning.
  A legitimate re-run requires `force_reclaim_reason` -- a mandatory,
  non-blank, human-written justification, itself logged -- not a bare
  boolean override.

`strategy_id` is a required keyword-only parameter on `load_holdout_klines`
even though CLAUDE.md's Build-section code snippet for this function only
shows `i_understand_this_is_holdout_data` explicitly -- the single-access
enforcement described in the same design section is defined *in terms of*
`strategy_id` ("scans the log for an existing `holdout_access` record for
this `strategy_id`"), so it must be a real parameter, not an omission.
Documented here rather than silently added. See
`.planning/sr-c-walkforward-holdout.md` for the rest of this task's
judgment calls.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from backtest.kline import Kline
from data.backfill import DEFAULT_DB_PATH
from data.bingx_klines import KlineRow
from data.store import connect, fetch_klines
from research import experiment_log

logger = logging.getLogger(__name__)

# Relative to cwd, same convention as `data.backfill.DEFAULT_DB_PATH` and
# `research.experiment_log.DEFAULT_RUNS_PATH` -- callers running for real
# are expected to run from the repo root; tests always override.
DEFAULT_HOLDOUT_CONFIG_PATH = "configs/research/holdout.json"


class HoldoutAlreadyClaimedError(RuntimeError):
    """Raised when `load_holdout_klines` is called for a `strategy_id`
    that already has a recorded `holdout_access` entry in
    `runs/experiments.jsonl`, and no (non-blank) `force_reclaim_reason`
    was given to deliberately override that.
    """


def load_holdout_config(path: str | Path = DEFAULT_HOLDOUT_CONFIG_PATH) -> dict:
    """Read the git-tracked holdout cutoff config. See
    `configs/research/holdout.json` for the actual committed value and
    `.planning/sr-c-walkforward-holdout.md` for the reasoning behind it.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _kline_row_to_kline(row: KlineRow) -> Kline:
    """`KlineRow` (ms-timestamp-keyed, `data/`'s wire/storage format) ->
    `Kline` (datetime-keyed, `backtest/`'s internal simulation format).
    Integer floor-division (not `/1000`) avoids any float round-trip on
    the timestamp -- every stored `open_time_ms` is an exact multiple of
    1000 given the 900,000ms (15m) grid, so this is always exact, never
    an approximation.
    """
    return Kline(
        open_time=datetime.fromtimestamp(row.open_time_ms // 1000, tz=timezone.utc),
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
    )


def _load_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    db_path: str | Path,
) -> list[Kline]:
    conn = connect(db_path)
    try:
        rows = fetch_klines(conn, symbol, interval, start_ms, end_ms)
    finally:
        conn.close()
    return [_kline_row_to_kline(row) for row in rows]


def load_research_klines(
    start_ms: int,
    end_ms: int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    holdout_config_path: str | Path = DEFAULT_HOLDOUT_CONFIG_PATH,
) -> list[Kline]:
    """Default data-loading path for every strategy-research use except
    the one-shot holdout confirmation run. Unconditionally clamps
    `end_ms` down to the holdout cutoff -- this function structurally
    cannot return holdout data, regardless of what a caller asks for.
    """
    config = load_holdout_config(holdout_config_path)
    cutoff_ms = config["holdout_cutoff_ms"]

    clamped_end_ms = min(end_ms, cutoff_ms)
    if clamped_end_ms < end_ms:
        logger.warning(
            "load_research_klines: requested end_ms=%d is at/after the holdout "
            "cutoff (holdout_cutoff_ms=%d) -- clamping end_ms to %d so no "
            "holdout data leaks into a research call",
            end_ms,
            cutoff_ms,
            clamped_end_ms,
        )

    return _load_klines(config["symbol"], config["interval"], start_ms, clamped_end_ms, db_path)


def load_holdout_klines(
    start_ms: int,
    end_ms: int,
    *,
    strategy_id: str,
    i_understand_this_is_holdout_data: bool,
    force_reclaim_reason: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    holdout_config_path: str | Path = DEFAULT_HOLDOUT_CONFIG_PATH,
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
) -> list[Kline]:
    """The loud, explicit holdout-data path. Raises `ValueError` unless
    `i_understand_this_is_holdout_data=True` is passed explicitly, and
    `HoldoutAlreadyClaimedError` if `strategy_id` already has a recorded
    `holdout_access` entry (see module docstring) and no non-blank
    `force_reclaim_reason` override was given.

    The single-access claim (the `holdout_access` log entry) is only
    written after data is actually successfully read -- a call that fails
    for some other reason (e.g. a bad range) does not burn the claim.
    """
    if not i_understand_this_is_holdout_data:
        raise ValueError(
            "load_holdout_klines requires i_understand_this_is_holdout_data=True "
            "-- this loads the untouched validation holdout split; see "
            "CLAUDE.md's Strategy Research Methodology section."
        )
    if not strategy_id:
        raise ValueError("strategy_id is required and must be non-blank")

    existing = _find_holdout_access(strategy_id, runs_path)
    if existing is not None:
        if force_reclaim_reason is None or not force_reclaim_reason.strip():
            raise HoldoutAlreadyClaimedError(
                f"strategy_id={strategy_id!r} already has a recorded holdout_access "
                f"entry (accessed_at={existing.get('accessed_at')!r}) -- holdout data "
                "may only be accessed once per strategy_id. Pass a non-blank "
                "force_reclaim_reason if this is a deliberate, justified re-run "
                "(e.g. a metrics-bug fix discovered after the first holdout run)."
            )
        logger.warning(
            "load_holdout_klines: re-claiming holdout access for strategy_id=%r "
            "(previous access at %r) -- force_reclaim_reason=%r",
            strategy_id,
            existing.get("accessed_at"),
            force_reclaim_reason,
        )

    config = load_holdout_config(holdout_config_path)
    cutoff_ms = config["holdout_cutoff_ms"]
    if start_ms < cutoff_ms:
        raise ValueError(
            f"start_ms={start_ms} is before holdout_cutoff_ms={cutoff_ms} -- "
            "load_holdout_klines only ever serves data at/after the cutoff; "
            "use load_research_klines for anything before it."
        )

    klines = _load_klines(config["symbol"], config["interval"], start_ms, end_ms, db_path)

    experiment_log.log_holdout_access(
        strategy_id=strategy_id,
        symbol=config["symbol"],
        interval=config["interval"],
        start_ms=start_ms,
        end_ms=end_ms,
        force_reclaim_reason=force_reclaim_reason,
        runs_path=runs_path,
    )

    return klines


def _find_holdout_access(strategy_id: str, runs_path: str | Path) -> dict | None:
    """Scan `runs_path` for the most recent `holdout_access` record for
    `strategy_id`, or `None` if there isn't one. "Most recent" matters
    for the force-reclaim path: after a legitimate override, a further
    unauthorized access must be checked against the newest claim, not the
    original one.
    """
    match: dict | None = None
    for record in experiment_log.read_records(runs_path):
        if record.get("record_type") == "holdout_access" and record.get("strategy_id") == strategy_id:
            match = record
    return match
