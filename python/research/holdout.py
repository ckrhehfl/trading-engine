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

## `holdout_side`: which side of the cutoff the holdout actually is

Added by Strategy Research Task T (`.planning/sr-t-daily-data-path.md`).
An optional config key, `"before"` or `"after"`, defaulting to
`"after"` -- so `configs/research/holdout.json` and
`configs/research/holdout_1h.json`, neither of which sets it, behave
exactly as they did before this key existed.

    "after"  (default): holdout = [cutoff, inf), research = (-inf, cutoff)
    "before":           holdout = (-inf, cutoff), research = [cutoff, inf)

**`"before"` looks wrong at a glance and is not.** The reflex is that a
holdout must be the *most recent* data, because the usual reason to hold
data out is to simulate "the future the model hasn't seen yet." That
framing is right when the alternative is training on the future and
testing on the past. It is not what a holdout is *for* here. CLAUDE.md's
Strategy Research Methodology defines the holdout by what has been done
to the data, not by where it sits on the calendar: "A holdout data split
must exist and stay untouched until a strategy is otherwise ready for
paper trading... Touching it converts it from a validation check into
just more training data." The property being protected is **no decision
in this project has been informed by the contents of this data**.

For this project's 1d dataset, that property holds for the EARLY window,
not the late one. Every one of the 1,839 backtest runs in
`runs/experiments.jsonl` has `data_range.start_ms >=
2024-04-27T10:00:00Z` (the floor of BingX's 1h retention, which is where
primary research moved in Strategy Research Task F) -- so eight
strategy families' worth of iteration, parameter search and
eligibility-bar judgment have all been shaped by data from 2024-04-27
onward, and by *none* before it. Reserving a trailing slice of a 1d
series as its holdout would reserve data this project has already looked
at hard, through a different timeframe's lens; reserving the early slice
reserves data no trial has ever touched. Choosing the calendar-late
window here would satisfy the convention and defeat the purpose.

`load_research_*`'s clamp therefore moves with the side: under `"after"`
it clamps `end_ms` down to the cutoff (nothing at/after it can leak),
under `"before"` it clamps `start_ms` up to the cutoff (nothing before it
can leak). `load_holdout_klines`'s guard mirrors it: `"after"` rejects a
`start_ms` before the cutoff, `"before"` rejects an `end_ms` after it.
Everything else about the holdout path -- the explicit
`i_understand_this_is_holdout_data` keyword, the single-access claim per
`strategy_id`, `force_reclaim_reason` -- is unchanged and side-agnostic.

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
from data.bingx_funding import FundingRow
from data.bingx_klines import KlineRow
from data.store import connect, fetch_funding_rates, fetch_klines
from metrics.funding import FundingRate
from research import experiment_log

logger = logging.getLogger(__name__)

# Relative to cwd, same convention as `data.backfill.DEFAULT_DB_PATH` and
# `research.experiment_log.DEFAULT_RUNS_PATH` -- callers running for real
# are expected to run from the repo root; tests always override.
DEFAULT_HOLDOUT_CONFIG_PATH = "configs/research/holdout.json"

# Which side of `holdout_cutoff_ms` the HOLDOUT occupies (research data
# is always the other side). See the module docstring for why "before"
# is a legitimate -- and for the 1d dataset, the only correct -- choice.
HOLDOUT_SIDE_BEFORE = "before"
HOLDOUT_SIDE_AFTER = "after"
VALID_HOLDOUT_SIDES = (HOLDOUT_SIDE_BEFORE, HOLDOUT_SIDE_AFTER)

# A config that doesn't name a side is the trailing-holdout config this
# project had before Task T -- `configs/research/holdout.json` (15m) and
# `configs/research/holdout_1h.json` (1h) are both in that category, and
# must keep behaving identically to before the key existed.
DEFAULT_HOLDOUT_SIDE = HOLDOUT_SIDE_AFTER


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

    Returns the file's contents unmodified -- no default is injected for
    an absent `holdout_side`, so a config without the key still reads
    back exactly as written. An *invalid* `holdout_side` fails loud here
    (`ValueError`), at load time, rather than silently defaulting to
    trailing-holdout behavior: a typo'd side on a `"before"` config would
    otherwise hand a caller the wrong half of the dataset while looking
    like it worked, which for a holdout is the exact failure this whole
    module exists to prevent.
    """
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    resolve_holdout_side(config)  # validation only; the dict is returned as-is
    return config


def resolve_holdout_side(config: dict) -> str:
    """Return `config`'s `holdout_side`, defaulting to
    `DEFAULT_HOLDOUT_SIDE` ("after") when absent. Raises `ValueError` for
    any other value.
    """
    side = config.get("holdout_side", DEFAULT_HOLDOUT_SIDE)
    if side not in VALID_HOLDOUT_SIDES:
        raise ValueError(
            f"holdout_side={side!r} is not one of {VALID_HOLDOUT_SIDES} -- "
            "it names which side of holdout_cutoff_ms the HOLDOUT occupies "
            "(research data is the other side); omit the key for the "
            f"default ({DEFAULT_HOLDOUT_SIDE!r}, a trailing holdout)."
        )
    return side


def _clamp_research_range(
    caller: str,
    start_ms: int,
    end_ms: int,
    cutoff_ms: int,
    side: str,
) -> tuple[int, int]:
    """Clamp a research request away from whichever side of `cutoff_ms`
    the holdout occupies, warning when it actually clamps something.

    Shared by `load_research_klines` and `load_research_funding` so the
    two can't drift apart -- a research loader that clamped the wrong end
    would silently serve holdout data, and there is no test a caller
    could write to notice.

    Re-rejects an unknown `side` itself (`ValueError`) rather than
    falling through to either branch, even though both current callers
    already pre-validate via `resolve_holdout_side` (CodeRabbit review
    finding on Task T's PR). An `if after: ... else: ...` shape would
    treat a typo'd side as `"before"` and silently invert which end gets
    clamped -- serving holdout data to a research caller, the exact
    failure this module's fail-loud discipline exists to prevent. Cheaper
    to enforce here than to rely on every future caller having validated.
    """
    if side == HOLDOUT_SIDE_AFTER:
        clamped_start_ms, clamped_end_ms = start_ms, min(end_ms, cutoff_ms)
        clamped = clamped_end_ms < end_ms
        detail = f"requested end_ms={end_ms} is at/after"
    elif side == HOLDOUT_SIDE_BEFORE:
        clamped_start_ms, clamped_end_ms = max(start_ms, cutoff_ms), end_ms
        clamped = clamped_start_ms > start_ms
        detail = f"requested start_ms={start_ms} is before"
    else:
        raise ValueError(
            f"_clamp_research_range: unknown holdout_side={side!r} -- "
            f"expected one of {VALID_HOLDOUT_SIDES}"
        )

    if clamped:
        logger.warning(
            "%s: %s the holdout cutoff (holdout_cutoff_ms=%d, holdout_side=%r) "
            "-- clamping the requested range to [%d, %d) so no holdout data "
            "leaks into a research call",
            caller,
            detail,
            cutoff_ms,
            side,
            clamped_start_ms,
            clamped_end_ms,
        )

    return clamped_start_ms, clamped_end_ms


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


def _funding_row_to_rate(row: FundingRow) -> FundingRate:
    """`FundingRow` (ms-timestamp-keyed, `data/`'s wire/storage format) ->
    `FundingRate` (datetime-keyed, `metrics/`'s internal format). Mirrors
    `_kline_row_to_kline`'s conversion pattern exactly -- this is the
    conversion function `metrics/funding.py`'s own docstring already names
    as the established precedent to follow for `FundingRow` -> `FundingRate`.
    """
    return FundingRate(
        funding_time=datetime.fromtimestamp(row.funding_time_ms // 1000, tz=timezone.utc),
        funding_rate=row.funding_rate,
        mark_price=row.mark_price,
    )


def _load_funding_rates(
    symbol: str,
    start_ms: int,
    end_ms: int,
    db_path: str | Path,
) -> list[FundingRate]:
    conn = connect(db_path)
    try:
        rows = fetch_funding_rates(conn, symbol, start_ms, end_ms)
    finally:
        conn.close()
    return [_funding_row_to_rate(row) for row in rows]


def load_research_klines(
    start_ms: int,
    end_ms: int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    holdout_config_path: str | Path = DEFAULT_HOLDOUT_CONFIG_PATH,
) -> list[Kline]:
    """Default data-loading path for every strategy-research use except
    the one-shot holdout confirmation run. Unconditionally clamps the
    requested range away from the holdout side of the cutoff -- `end_ms`
    down to it under the default `holdout_side="after"`, `start_ms` up to
    it under `holdout_side="before"` (see the module docstring). This
    function structurally cannot return holdout data, regardless of what
    a caller asks for.

    A request that lies *entirely* on the holdout side clamps to an empty
    range and therefore raises `ValueError` (from `require_valid_range`,
    via `fetch_klines`) rather than returning `[]` -- pre-existing
    behavior on the `"after"` side, preserved deliberately and mirrored
    on the `"before"` side: asking a research loader for nothing but
    holdout data is a caller bug worth failing loudly on.
    """
    config = load_holdout_config(holdout_config_path)
    cutoff_ms = config["holdout_cutoff_ms"]

    clamped_start_ms, clamped_end_ms = _clamp_research_range(
        "load_research_klines", start_ms, end_ms, cutoff_ms, resolve_holdout_side(config)
    )

    return _load_klines(
        config["symbol"], config["interval"], clamped_start_ms, clamped_end_ms, db_path
    )


def load_research_funding(
    start_ms: int,
    end_ms: int,
    *,
    symbol: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    holdout_config_path: str | Path = DEFAULT_HOLDOUT_CONFIG_PATH,
) -> list[FundingRate]:
    """Funding-rate counterpart to `load_research_klines` -- same
    unconditional holdout-cutoff clamp, applied to whichever end of the
    range the config's `holdout_side` puts the holdout on (`end_ms`
    clamped down to `holdout_cutoff_ms` under the default `"after"`;
    `start_ms` clamped up to it under `"before"` -- see the module
    docstring), so a funding series loaded through this function can
    never carry rows from the holdout side of whatever cutoff
    `holdout_config_path` names, matching this project's holdout
    discipline for price data (CLAUDE.md's Strategy Research Methodology).

    `symbol` defaults to `None`, meaning "use `holdout_config_path`'s own
    `symbol`" -- the SAME symbol `load_research_klines` would use for that
    same config path, so a caller loading both klines and funding for one
    holdout config without explicitly naming a symbol is guaranteed to get
    a matched pair, never a silent klines/funding symbol mismatch (a real
    CodeRabbit review finding on this task's PR: a caller passing a non-
    BTC holdout config while forgetting to also pass `symbol` here would
    otherwise have silently mixed a BTC-USDT funding series into a
    different symbol's backtest -- wrong data silently, worse than an
    explicit failure). An explicitly supplied `symbol` still overrides the
    config's own value, e.g. for a deliberate cross-symbol experiment.
    Funding rate has no `interval` concept (see `data/bingx_funding.py`'s
    module docstring) and therefore no dedicated per-timeframe holdout
    config the way klines do (`configs/research/holdout.json` for 15m vs.
    `configs/research/holdout_1h.json` for 1h) -- the SAME `holdout_
    cutoff_ms`/`symbol` this call's config names governs both the kline
    data a caller loads via `load_research_klines` and the funding data
    loaded here.

    Deliberately no `load_holdout_funding` counterpart yet -- no task has
    needed one (this project's funding-rate strategy work so far never
    touches the holdout split; see `.planning/sr-n-funding-rate-
    strategy.md`), and adding one speculatively, ahead of any real caller,
    would go against this project's "touch only what the task requires"
    discipline. Add it, mirroring `load_holdout_klines`, when a real task
    actually needs funding-inclusive holdout access.
    """
    config = load_holdout_config(holdout_config_path)
    cutoff_ms = config["holdout_cutoff_ms"]
    resolved_symbol = symbol if symbol is not None else config["symbol"]

    clamped_start_ms, clamped_end_ms = _clamp_research_range(
        "load_research_funding", start_ms, end_ms, cutoff_ms, resolve_holdout_side(config)
    )

    return _load_funding_rates(resolved_symbol, clamped_start_ms, clamped_end_ms, db_path)


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

    The requested range must lie wholly on the config's holdout side of
    the cutoff: under the default `holdout_side="after"` that means
    `start_ms >= holdout_cutoff_ms`; under `holdout_side="before"` it
    means `end_ms <= holdout_cutoff_ms` (the range is half-open, so
    `end_ms == cutoff` is the full holdout). Either violation raises
    `ValueError` -- this function never quietly trims a request down to
    the holdout side the way the research loaders clamp, because a
    caller asking the holdout door for research data has misunderstood
    which split they're in, and that is worth stopping.

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
    side = resolve_holdout_side(config)
    if side == HOLDOUT_SIDE_AFTER and start_ms < cutoff_ms:
        raise ValueError(
            f"start_ms={start_ms} is before holdout_cutoff_ms={cutoff_ms} -- "
            "this config's holdout_side is 'after', so load_holdout_klines only "
            "ever serves data at/after the cutoff; use load_research_klines for "
            "anything before it."
        )
    if side == HOLDOUT_SIDE_BEFORE and end_ms > cutoff_ms:
        raise ValueError(
            f"end_ms={end_ms} is after holdout_cutoff_ms={cutoff_ms} -- "
            "this config's holdout_side is 'before', so load_holdout_klines only "
            "ever serves data before the cutoff; use load_research_klines for "
            "anything at/after it."
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
