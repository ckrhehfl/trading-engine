"""BingX historical funding-rate client -- public, unauthenticated
`GET /openApi/swap/v2/quote/fundingRate`. Stdlib-only (`urllib`), no
`requests`/`httpx` -- same discipline as `bingx_klines.py`, which this
module mirrors in structure and pagination approach (see that module's
docstring and CLAUDE.md's "Strategy Research Operational Design" data-
pipeline section).

`base_url` is always a caller-supplied argument, never a literal in this
module -- same "caller decides the host" pattern as `bingx_klines.py`;
see `.github/workflows/bingx-hostname-guard.yml`.

Response shape and pagination behavior verified directly against the
live production endpoint 2026-07-27 (see `.planning/sr-m-funding-rate-
pipeline.md` for the full investigation, including the binary-search-
style probing this module's design is based on):

- Envelope: `{"code": 0, "msg": "", "data": [...] | null}` -- same
  `code`/`msg`/`data` shape as every other BingX endpoint already
  documented in CLAUDE.md, **except** `data` is `null` (not `[]`) when
  no rows match the requested range. Confirmed for both a genuinely
  out-of-retention range and an in-retention range that simply has no
  funding event inside it (funding settles only every 8h, so most
  narrow ranges are empty) -- `null` is this endpoint's general "no
  rows matched" marker, not exclusively a retention-boundary signal.
- Each row: `{"symbol": "...", "fundingRate": "...", "fundingTime":
  <int ms>, "markPrice": "..."}`. `fundingRate`/`markPrice` come back as
  quoted strings; `fundingTime` is a bare integer.
  `json.loads(..., parse_float=Decimal)` is used for the same defensive
  reason as `bingx_klines.py`.
- **Ordering is newest-first (descending `fundingTime`) within a page**
  -- same as klines. `iter_funding_range`'s cursor is always
  `max(row.funding_time_ms) + step`, never array position.
- **Silent capping to the newest rows, same mechanism as klines**: a
  request whose `[startTime, endTime)` span holds more rows than
  `limit` returns only the `limit` rows closest to `endTime`, not the
  oldest. Confirmed empirically the same way `bingx_klines.py`'s module
  docstring describes for klines.
- **`limit` is a hard server-side error above 1000, not a silent
  clamp** -- a real, verified difference from klines. `limit=2000`
  returned `{"code": 109400, "msg": "Invalid parameters,
  err:limit: This field must be less than or equal to 1000. ", "data":
  {}}`. `fetch_funding_page` validates `limit` client-side (same
  `1..1000` bound as klines) so this error is never actually triggered
  by anything in this pipeline.
- **The critical, genuinely new finding (this endpoint has no klines
  equivalent): `data: null` is flaky/non-deterministic for older
  ranges, not a reliable "no data here" signal.** Repeated identical
  requests (same exact `[startTime, endTime)`, several seconds apart)
  against a range independently confirmed to contain real, settled
  funding rows returned `null` on some calls and the real rows on
  others -- observed rates from roughly 1-in-6 to 1-in-2 depending on
  how far back the range was, with no clean deterministic retention
  cutoff found even after extensive binary-search-style probing (see
  `.planning/sr-m-funding-rate-pipeline.md`). A genuinely out-of-
  retention range (e.g. 2020) returned `null` consistently across
  10-15 repeated trials, so `null` does still mean "no data" reliably
  once *far enough* back -- it just isn't safe to trust after a single
  call anywhere near the edge of real history. `fetch_funding_page`
  therefore retries a `null` response up to `_MAX_NULL_RETRIES` times
  (a fixed delay between attempts, deliberately independent of the
  HTTP-transport-level retry in `_get_with_retry`) before treating it
  as genuinely empty. Even this is a mitigation, not a guarantee --
  `backfill_funding.py`'s resumability (`find_missing_funding_ranges`
  + idempotent re-run, identical mechanism to `backfill.py`) is what
  ultimately makes a multi-run backfill complete, the same way it
  already does for transient HTTP failures.
- **Real historical `fundingTime` values are NOT always aligned to the
  modern 8h/28,800,000ms grid -- a second genuinely new finding, only
  discovered via a real end-to-end backfill run, not the earlier probing
  above.** A real `backfill_funding.py` run against production
  (2026-07-28, see `.planning/sr-m-funding-rate-pipeline.md`) found:
  (a) every row from 2020-11-29 through 2021-01-05 settles on a
  04:00/12:00/20:00 UTC grid, a fixed 4h offset from the
  00:00/08:00/16:00 UTC grid every later row uses -- a real historical
  schedule change on BingX's side, not a parsing bug; (b) one isolated
  off-grid row at 2021-11-11T18:00:00Z (2h after the preceding
  16:00:00Z row, 6h before the following day's 00:00:00Z row) with no
  surrounding pattern, i.e. a genuine one-off. **This means `start_ms`/
  `end_ms` grid-alignment is deliberately NOT enforced anywhere in this
  module** (unlike `bingx_klines.py`, where kline `open_time` alignment
  to its interval grid is a real, verified invariant) -- `_validate_range`
  below only checks `start_ms < end_ms`. An earlier version of this
  module reused `data._grid.require_valid_range` (the same strict
  alignment check klines uses) and it broke on real data: a stored
  row's own off-grid timestamp, used as a `find_missing_funding_ranges`
  gap boundary, got rejected by `iter_funding_range`'s range validation
  on the very next backfill rerun. `FUNDING_INTERVAL_MS` is kept as a
  documented *typical* cadence and chunk-sizing hint (see
  `iter_funding_range`'s `chunk_span`) -- a reasonable approximation for
  pagination width, never a hard per-row alignment guarantee.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator

MAX_FUNDING_ROWS_PER_REQUEST = 1000

# Funding's *typical* cadence: every 8 hours, most commonly aligned to
# the 00:00/08:00/16:00 UTC daily grid. Used here purely as a chunk-
# sizing hint for pagination width (see iter_funding_range's chunk_span)
# -- NOT a hard per-row alignment guarantee. A real backfill (2026-07-28,
# see this module's docstring and .planning/sr-m-funding-rate-
# pipeline.md) found real historical rows that don't land on this grid
# at all (a 2020-2021 period on a different, 4h-offset grid, plus an
# isolated one-off), so range-boundary validation in this module
# deliberately does not enforce alignment to this value the way
# data._grid.require_valid_range does for klines' genuinely-verified
# grid. Not part of data._grid.INTERVAL_MS for the same reason funding
# has no "interval" concept to begin with (see module docstring).
FUNDING_INTERVAL_MS = 28_800_000

# Same fixed-delay mitigation as bingx_klines.py's INTER_REQUEST_DELAY_S
# for the same reason (no documented rate limit for this public
# endpoint) -- not measured/tuned, a conservative default.
INTER_REQUEST_DELAY_S = 0.25

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_RETRY_BASE_DELAY_S = 1.0

# Separate from the HTTP-transport retry above -- this retries a
# *successfully parsed* `data: null` response, which real BingX has been
# confirmed (see module docstring) to return flakily even when the
# requested range genuinely has data. 5 attempts at an observed worst-
# case ~50% per-call miss rate leaves roughly a 3% chance of a false
# "empty" -- consistent with this pipeline's other retry counts, not
# separately tuned against a measured SLA (none is published).
_MAX_NULL_RETRIES = 5
_NULL_RETRY_DELAY_S = 0.5

_FUNDING_PATH = "/openApi/swap/v2/quote/fundingRate"


class BingXFundingError(RuntimeError):
    """Raised on a non-zero `code` envelope, a malformed/unparseable
    response body, or a non-retryable HTTP error.
    """


def _validate_range(start_ms: int, end_ms: int) -> None:
    """`start_ms < end_ms` only -- deliberately NOT grid-alignment
    (unlike `data._grid.require_valid_range`, which klines uses). See
    this module's docstring for why: real funding timestamps are not
    always aligned to `FUNDING_INTERVAL_MS`, confirmed via a real
    backfill run, so enforcing alignment here would reject legitimate
    range boundaries derived from real stored data.
    """
    if start_ms >= end_ms:
        raise ValueError(f"start_ms ({start_ms}) must be < end_ms ({end_ms})")


@dataclass(frozen=True)
class FundingRow:
    """One funding settlement as returned by BingX -- ms-timestamp-keyed,
    matching the wire format (mirrors `bingx_klines.KlineRow`'s
    relationship to `backtest.kline.Kline`; see `research/holdout.py`'s
    `_kline_row_to_kline` for this codebase's established wire-type ->
    internal-type conversion pattern, which `metrics.funding.FundingRate`
    follows for this type too).
    """

    funding_time_ms: int
    funding_rate: Decimal
    mark_price: Decimal


def fetch_funding_page(
    base_url: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    limit: int = MAX_FUNDING_ROWS_PER_REQUEST,
) -> list[FundingRow]:
    """Fetch one page of funding-rate history for the half-open range
    `[start_ms, end_ms)`.

    Does **not** guarantee the returned rows cover the full requested
    range -- same silent-capping caveat as
    `bingx_klines.fetch_klines_page`. Callers needing a large range fully
    covered must use `iter_funding_range`.

    Raises `ValueError` for an inverted range (`start_ms >= end_ms`) or
    an out-of-range `limit` -- deliberately **not** for a misaligned
    `start_ms`/`end_ms` (see `_validate_range` and this module's
    docstring for why grid alignment isn't enforced here, unlike
    `bingx_klines.fetch_klines_page`). Raises `BingXFundingError` for a
    non-zero `code`, a malformed body, or a non-retryable HTTP error.

    A `data: null` response is retried (see `_MAX_NULL_RETRIES` and the
    module docstring's flaky-history finding) before being treated as
    genuinely empty -- returned as `[]` either way, so callers never see
    `null` directly.
    """
    _validate_range(start_ms, end_ms)
    if not (1 <= limit <= MAX_FUNDING_ROWS_PER_REQUEST):
        raise ValueError(f"limit must be in 1..{MAX_FUNDING_ROWS_PER_REQUEST}, got {limit}")

    query = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
        }
    )
    url = f"{base_url}{_FUNDING_PATH}?{query}"

    raw_rows: object = None
    for attempt in range(_MAX_NULL_RETRIES):
        body = _get_with_retry(url)

        try:
            payload = json.loads(body, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise BingXFundingError(f"malformed JSON response from {url}: {exc}") from exc

        if not isinstance(payload, dict):
            raise BingXFundingError(f"response body is not a JSON object: {payload!r}")

        code = payload.get("code")
        if code != 0:
            raise BingXFundingError(f"BingX returned non-zero code={code!r} msg={payload.get('msg')!r}")

        raw_rows = payload.get("data")
        if raw_rows is not None:
            break
        if attempt < _MAX_NULL_RETRIES - 1:
            time.sleep(_NULL_RETRY_DELAY_S)
        # else: exhausted retries -- fall through, treat as genuinely empty below.

    if raw_rows is None:
        return []
    if not isinstance(raw_rows, list):
        raise BingXFundingError(f"BingX response 'data' is neither a list nor null: {raw_rows!r}")

    rows = [_parse_row(row) for row in raw_rows]

    if len(rows) > limit:
        # Same fail-loud reasoning as bingx_klines.py: real BingX never
        # does this, so it means either BingX's behavior changed or this
        # fake-server-in-tests has a bug.
        raise BingXFundingError(f"BingX returned {len(rows)} rows, more than requested limit={limit}")

    return rows


def iter_funding_range(
    base_url: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    limit: int = MAX_FUNDING_ROWS_PER_REQUEST,
) -> Iterator[FundingRow]:
    """Walk the half-open range `[start_ms, end_ms)` in `<= limit`-row
    chunks, yielding every `FundingRow` found -- structurally identical
    to `bingx_klines.iter_klines_range` (see that function's docstring
    for the cursor-derivation and empty-chunk-is-not-an-error reasoning,
    which applies unchanged here). The one behavioral difference is
    entirely inside `fetch_funding_page` (the null-retry above), which
    this function doesn't need to know about -- an empty page here can
    mean either a genuine gap or an exhausted null-retry, and both are
    handled the same way: advance the cursor by the full chunk width and
    continue.
    """
    _validate_range(start_ms, end_ms)
    if not (1 <= limit <= MAX_FUNDING_ROWS_PER_REQUEST):
        raise ValueError(f"limit must be in 1..{MAX_FUNDING_ROWS_PER_REQUEST}, got {limit}")

    cursor = start_ms
    chunk_span = limit * FUNDING_INTERVAL_MS
    first_request = True

    while cursor < end_ms:
        if not first_request:
            time.sleep(INTER_REQUEST_DELAY_S)
        first_request = False

        chunk_end = min(cursor + chunk_span, end_ms)
        page = fetch_funding_page(base_url, symbol, cursor, chunk_end, limit=limit)

        if page:
            max_time = max(row.funding_time_ms for row in page)
            cursor = max_time + FUNDING_INTERVAL_MS
            yield from page
        else:
            cursor = chunk_end


def _parse_row(row: object) -> FundingRow:
    if not isinstance(row, dict):
        raise BingXFundingError(f"malformed funding row (not an object): {row!r}")
    try:
        return FundingRow(
            funding_time_ms=int(row["fundingTime"]),
            funding_rate=Decimal(row["fundingRate"]),
            mark_price=Decimal(row["markPrice"]),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise BingXFundingError(f"malformed funding row: {row!r}") from exc


def _get_with_retry(url: str) -> str:
    # Byte-for-byte the same HTTP-transport retry logic as
    # bingx_klines.py's _get_with_retry, including the read-time OSError
    # handling CodeRabbit review found necessary there (see
    # .planning/sr-a-data-pipeline.md's "CodeRabbit review findings") --
    # duplicated rather than imported, since bingx_klines.py's version is
    # a private (`_`-prefixed) helper not meant for cross-module reuse,
    # and this module's docstring already frames it as mirroring
    # bingx_klines.py's structure rather than depending on it.
    last_exc: BaseException | None = None
    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            time.sleep(_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))
        request = urllib.request.Request(url, headers={"User-Agent": "trading-engine-research/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in _RETRYABLE_STATUSES:
                raise BingXFundingError(f"BingX returned non-retryable HTTP {exc.code} for {url}") from exc
            continue
        except urllib.error.URLError as exc:
            last_exc = exc
            continue
        except OSError as exc:
            last_exc = exc
            continue

    raise BingXFundingError(f"exceeded {_MAX_RETRIES} retries fetching {url}: {last_exc}") from last_exc
